"""ByteBarn entry point: qasync event loop + engine + main window."""

from __future__ import annotations

import asyncio
import encodings.idna  # noqa: F401 — preload: frozen apps can fail codec
import signal
import sys             # lookup ("unknown encoding: idna") when a network
from pathlib import Path  # thread triggers the first import

import qasync
from PySide6.QtWidgets import QApplication

from .app.icon import app_icon
from .app.main_window import MainWindow
from .app.theme import apply_theme
from .engine.facade import Engine


def scrub_qt_env(environ: dict, pyside_dir: str) -> list[str]:
    """Drop Qt environment leaked from another Qt install (e.g. a frozen
    /Applications/ByteBarn.app bundle whose launcher spawned us, or whose env
    survived an updater restart).

    Loading a platform plugin or framework from a *different* Qt copy makes
    QGuiApplication::createPlatformIntegration() qFatal/abort — the classic
    two-Qt crash. Rules:

    - plugin-path vars that don't point into our own PySide6 are removed
    - DYLD path entries mentioning a foreign Qt are filtered out
    - a headless QT_QPA_PLATFORM (offscreen/minimal) is removed — the GUI
      entry point never wants an invisible window; tests set it themselves
      and never come through main()

    Returns the names of the variables that were changed."""
    removed: list[str] = []
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        value = environ.get(key)
        if value and pyside_dir not in value:
            environ.pop(key, None)
            removed.append(key)
    for key in ("DYLD_FRAMEWORK_PATH", "DYLD_LIBRARY_PATH"):
        value = environ.get(key)
        if not value:
            continue
        parts = [p for p in value.split(":") if p]
        kept = [p for p in parts if "Qt" not in p or pyside_dir in p]
        if kept != parts:
            removed.append(key)
            if kept:
                environ[key] = ":".join(kept)
            else:
                environ.pop(key, None)
    if environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal"):
        environ.pop("QT_QPA_PLATFORM", None)
        removed.append("QT_QPA_PLATFORM")
    return removed


def main() -> None:
    import os

    import PySide6

    # Launched from Finder/Dock we inherit launchd's bare PATH, so Homebrew,
    # npm and cargo tooling (claude, node, rg…) is invisible to every
    # subprocess we spawn. Ask the login shell once, here, before anything
    # tries to run a command.
    from .engine.shell_path import hydrate_path

    hydrate_path()

    scrubbed = scrub_qt_env(os.environ, str(Path(PySide6.__file__).parent))
    if scrubbed:
        print(f"[crew] ignored foreign Qt environment: {', '.join(scrubbed)}",
              file=sys.stderr)

    # and make sure our own Qt is first for the dynamic linker
    venv_qt = Path(PySide6.__file__).parent / "Qt" / "lib"
    if venv_qt.exists():
        cur = os.environ.get("DYLD_FRAMEWORK_PATH", "")
        os.environ["DYLD_FRAMEWORK_PATH"] = str(venv_qt) + (":" + cur if cur else "")
        cur_lib = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = str(venv_qt) + (":" + cur_lib if cur_lib else "")

    # the preview panel creates a QWebEngineView lazily; WebEngine requires
    # shared GL contexts to be enabled before the QApplication exists
    from PySide6.QtCore import Qt as _Qt

    QApplication.setAttribute(_Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("ByteBarn")
    app.setWindowIcon(app_icon())
    # Normal macOS behaviour: closing the last window quits the app.
    # The qasync loop is stopped via app.aboutToQuit below.
    app.setQuitOnLastWindowClosed(True)

    # directories are per session now — no startup picker. This only sets the
    # engine's config root (global + project .bytebarn); every session picks its
    # own working directory explicitly when created.
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).resolve()
    else:
        project_dir = Path.home()
        try:
            from .engine.config import GLOBAL_DIR, lenient_json_loads

            cfg_path = GLOBAL_DIR / "config.json"
            if cfg_path.exists():
                last = lenient_json_loads(cfg_path.read_text()).get("last_project", "")
                if last and Path(last).is_dir():
                    project_dir = Path(last)
        except Exception:
            pass

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    # Window owns graceful shutdown (closeEvent → await engine.stop → quit).
    # aboutToQuit only stops the qasync loop after that work finishes.
    app.aboutToQuit.connect(loop.stop)

    engine = Engine(project_dir)
    apply_theme(app, (engine.config.model_extra or {}).get("theme", "follow system"))
    from .app import sprites

    _extra = engine.config.model_extra or {}
    sprites.set_crew_style(
        _extra.get("crew_style")
        or ("waifu" if _extra.get("waifu") else "farm"))
    window = MainWindow(engine)
    window.setWindowIcon(app_icon())

    def _request_quit() -> None:
        # Route Ctrl-C / SIGTERM through closeEvent so async engine.stop runs.
        window.close()

    try:
        loop.add_signal_handler(signal.SIGINT, _request_quit)
        loop.add_signal_handler(signal.SIGTERM, _request_quit)
    except (NotImplementedError, RuntimeError):
        # Windows / restricted environments: leave default SIGINT behaviour.
        pass

    async def bootstrap() -> None:
        await engine.start()
        await window.bootstrap()
        window.show()

    with loop:
        loop.create_task(bootstrap())
        loop.run_forever()
    sys.exit(0)


if __name__ == "__main__":
    main()
