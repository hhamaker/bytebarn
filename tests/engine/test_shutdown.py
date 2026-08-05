"""Graceful shutdown: Engine.stop, sandbox kill-on-cancel, hooks side-effects."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.runner import RunHandle


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BYTEBARN_HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "small_model": "fake/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    if not getattr(eng, "_stopped", False):
        await eng.stop()


async def test_stop_is_idempotent(engine):
    await engine.stop()
    assert engine._stopped
    await engine.stop()  # second call must not throw
    assert engine._stopped


async def test_stop_aborts_and_drains_runs(engine):
    abort_seen = asyncio.Event()

    async def fake_run():
        handle = engine._runs["s1"]
        try:
            await handle.abort.wait()
            abort_seen.set()
            await asyncio.sleep(3600)  # cancelled after abort is set
        except asyncio.CancelledError:
            abort_seen.set()
            raise

    handle = RunHandle()
    engine._runs["s1"] = handle
    handle.task = asyncio.create_task(fake_run())
    await asyncio.sleep(0)  # let fake_run start and park on abort

    await engine.stop()

    assert engine._stopped
    assert handle.abort.is_set()
    assert handle.task.done()
    assert abort_seen.is_set()


async def test_stop_cancels_pending_permission_future(engine):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    engine._pending["req1"] = fut
    engine._pending_permissions.add("req1")

    await engine.stop()

    assert fut.cancelled() or fut.done()
    assert "req1" not in engine._pending
    assert "req1" not in engine._pending_permissions


async def test_stop_cancels_live_model_fetches(engine):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_fetch():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(slow_fetch())
    engine._live_model_fetches["fake"] = task
    await started.wait()

    await engine.stop()

    assert task.done()
    assert cancelled.is_set()
    assert engine._live_model_fetches == {}


async def test_stop_blocks_new_runs(engine):
    session = await engine.new_session()
    assert session.id not in engine._runs

    await engine.stop()
    engine._start_run(session)

    assert session.id not in engine._runs
    assert engine._stopped


async def test_on_run_finished_noop_after_stop(engine):
    await engine.stop()
    # Must not throw even for an unknown session (store is already closed).
    await engine.on_run_finished("nonexistent")


async def test_advance_goal_queue_noop_after_stop(engine):
    await engine.stop()
    # Must not throw / open new sessions after stop.
    await engine._advance_goal_queue("any-project")


# -- sandbox.run_command kill paths -------------------------------------------


async def _wait_for_pidfile(pidfile, timeout: float = 3.0) -> int:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pidfile.exists():
            text = pidfile.read_text().strip()
            if text:
                return int(text)
        await asyncio.sleep(0.05)
    raise AssertionError(f"pidfile not written: {pidfile}")


def _assert_dead(pid: int) -> None:
    """os.kill(pid, 0) succeeds if the process exists; OSError if it is gone."""
    with pytest.raises(OSError):
        os.kill(pid, 0)


async def test_run_command_kills_on_cancel(tmp_path):
    from bytebarn.engine.sandbox import run_command

    pidfile = tmp_path / "pid"
    cmd = f"echo $$ > {pidfile}; sleep 60"
    task = asyncio.create_task(run_command(cmd, tmp_path))
    pid = await _wait_for_pidfile(pidfile)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the kernel a beat to reap the process group.
    await asyncio.sleep(0.15)
    _assert_dead(pid)


async def test_run_command_kills_on_timeout(tmp_path):
    from bytebarn.engine.sandbox import run_command

    pidfile = tmp_path / "pid"
    cmd = f"echo $$ > {pidfile}; sleep 60"
    code, out, _backend = await run_command(cmd, tmp_path, timeout=0.3)
    pid = int(pidfile.read_text().strip())

    assert code == 1
    assert "timed out" in out
    await asyncio.sleep(0.15)
    _assert_dead(pid)


async def test_run_command_kills_on_abort(tmp_path):
    from bytebarn.engine.sandbox import run_command

    pidfile = tmp_path / "pid"
    cmd = f"echo $$ > {pidfile}; sleep 60"
    abort = asyncio.Event()
    task = asyncio.create_task(run_command(cmd, tmp_path, abort=abort, timeout=60))
    pid = await _wait_for_pidfile(pidfile)

    abort.set()
    code, out, _backend = await task

    assert code == 1
    assert "aborted" in out
    await asyncio.sleep(0.15)
    _assert_dead(pid)


# -- hooks._side kill on cancel -----------------------------------------------


async def test_hooks_side_kills_on_cancel(tmp_path):
    from bytebarn.engine.hooks import HookRunner

    runner = HookRunner()
    pidfile = tmp_path / "pid"
    cmd = f"echo $$ > '{pidfile}'; sleep 60"
    task = asyncio.create_task(runner._side(cmd, str(tmp_path)))
    pid = await _wait_for_pidfile(pidfile)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.15)
    _assert_dead(pid)
