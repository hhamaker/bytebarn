"""Crew entry point: qasync event loop + engine + main window."""

from __future__ import annotations

import asyncio
import encodings.idna  # noqa: F401 — preload: frozen apps can fail codec
import sys             # lookup ("unknown encoding: idna") when a network
from pathlib import Path  # thread triggers the first import

import qasync
from PySide6.QtWidgets import QApplication

from .app.icon import app_icon
from .app.main_window import MainWindow
from .app.theme import apply_theme
from .engine.facade import Engine


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Crew")
    app.setWindowIcon(app_icon())
    # the main window drives shutdown explicitly (engine cleanup first),
    # so don't let Qt quit out from under the asyncio teardown
    app.setQuitOnLastWindowClosed(False)

    # directories are per session now — no startup picker. Root the engine
    # at the last folder the user worked in (or home) and let each session
    # choose its own directory.
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
    app.aboutToQuit.connect(loop.stop)

    engine = Engine(project_dir)
    apply_theme(app, (engine.config.model_extra or {}).get("theme", "follow system"))
    window = MainWindow(engine)
    window.setWindowIcon(app_icon())

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
