"""MainWindow graceful shutdown — awaits engine.stop, drops late _fire work."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


async def test_shutdown_awaits_engine_stop(qapp, tmp_path, monkeypatch):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BYTEBARN_HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(
        json.dumps({"model": "fake/m", "small_model": "fake/m"})
    )

    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("hi")]))
    await engine.start()

    window = MainWindow(engine)
    # Avoid full bootstrap (watchfiles / refresh_all_models) — just exercise
    # the shutdown path that awaits engine.stop and marks _shutting_down.
    stop_called = asyncio.Event()
    real_stop = engine.stop

    async def tracking_stop():
        stop_called.set()
        await real_stop()

    engine.stop = tracking_stop  # type: ignore[method-assign]

    # Prevent QApplication.quit() from tearing down the shared module qapp.
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(QApplication, "instance", lambda: None)

    try:
        window._shutting_down = True  # closeEvent sets this before scheduling
        await window._shutdown()
        assert stop_called.is_set()
        assert engine._stopped
    finally:
        if not engine._stopped:
            await engine.stop()
        window.close()


async def test_fire_drops_work_when_shutting_down(qapp, tmp_path, monkeypatch):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BYTEBARN_HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(
        json.dumps({"model": "fake/m", "small_model": "fake/m"})
    )

    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("hi")]))
    await engine.start()
    window = MainWindow(engine)

    ran = False

    async def work():
        nonlocal ran
        ran = True

    try:
        # MainWindow may already have bg tasks (e.g. live-model fetch from
        # constructor wiring) — only assert our dropped work never runs and
        # does not enlarge the tracked set.
        before = set(getattr(window, "_bg_tasks", set()) or set())
        window._shutting_down = True
        window._fire(work())
        await asyncio.sleep(0)  # would run if scheduled
        assert ran is False
        after = set(getattr(window, "_bg_tasks", set()) or set())
        assert after == before
    finally:
        await engine.stop()
        window.close()


async def test_close_event_ignores_first_close(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication

    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BYTEBARN_HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(
        json.dumps({"model": "fake/m", "small_model": "fake/m"})
    )

    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("hi")]))
    await engine.start()
    window = MainWindow(engine)

    # Don't quit the shared qapp when shutdown finishes.
    monkeypatch.setattr(QApplication, "instance", lambda: None)

    stop_called = asyncio.Event()
    real_stop = engine.stop

    async def tracking_stop():
        stop_called.set()
        await real_stop()

    engine.stop = tracking_stop  # type: ignore[method-assign]

    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted() is False  # first close ignored
        assert window._shutting_down is True

        # Let the scheduled _shutdown complete.
        await asyncio.wait_for(stop_called.wait(), timeout=5.0)
        # Drain a few loops so _shutdown finishes past stop.
        for _ in range(10):
            await asyncio.sleep(0)
            if engine._stopped:
                break
        assert engine._stopped

        # Second close is allowed through.
        event2 = QCloseEvent()
        window.closeEvent(event2)
        assert event2.isAccepted()
    finally:
        if not engine._stopped:
            await engine.stop()
        window._shutting_down = True  # allow destroy without re-scheduling
        window.close()
