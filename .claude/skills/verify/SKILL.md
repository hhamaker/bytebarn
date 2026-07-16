---
name: verify
description: Drive the real Crew GUI offscreen to verify UI/engine changes end-to-end (no display needed) and capture screenshots.
---

# Verifying Crew changes

Crew is a PySide6 + qasync desktop app. Verify GUI/engine changes by driving
the real `MainWindow` offscreen — not by re-running pytest.

## Recipe

Write a harness script (scratchpad) that:

1. Builds a temp project dir + temp global dir with
   `config.json` = `{"model": "fake/m", "small_model": "fake/m", "onboarded": true}`.
2. Creates `Engine(proj, db_path=tmp/"db.sqlite", global_dir=gdir)` and
   registers `FakeProvider` from `crew.engine.providers.fake`
   (`engine.providers.register("fake", FakeProvider([text_turn("hi")]))`).
3. Instantiates `MainWindow(engine)`, `await window.bootstrap()`, `window.show()`.
4. Drives real methods (`_new_session`, `_refresh_sessions`, widget signals),
   inspects widget state, and captures `window.grab().save("out.png")` —
   grab() works offscreen.
5. Runs under qasync:
   ```python
   app = QApplication(sys.argv); loop = qasync.QEventLoop(app)
   asyncio.set_event_loop(loop)
   with loop: loop.run_until_complete(main(app, tmp))
   ```

Run with `QT_QPA_PLATFORM=offscreen .venv/bin/python harness.py`.

## Gotchas

- Never call `app.processEvents()` inside the async main — qasync re-enters
  tasks and corrupts the loop ("Cannot enter into task…"). `await
  asyncio.sleep(0.05)` pumps Qt events instead.
- Run harnesses with `python -u` (or `flush=True`) when redirecting to a log —
  buffered prints vanish if the process dies.

- `bootstrap()` schedules `_post_bootstrap` via `QTimer.singleShot(0, …)` —
  it fires during later awaits (qasync pumps Qt events), so it can interleave
  with your harness calls. On a fresh store it auto-creates a session.
- Cancel `window._tasks` and `await engine.stop()` before exiting or the
  loop hangs.
- Filter noise: `2>&1 | grep -v "^qt.qpa\|propagateSizeHints"`.
- CLI-only engine changes: `.venv/bin/python -m crew.cli "prompt" --project dir`
  is the no-GUI surface.
