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
    # On macOS, a frozen /Applications/Crew.app bundle can inject its own Qt
    # frameworks, colliding with the venv's PySide6 and causing force-quit.
    # Ensure the running interpreter's Qt is first in the dynamic linker path.
    import os
    from pathlib import Path as _P

    venv_qt = _P(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "PySide6" / "Qt" / "lib"
    if venv_qt.exists():
        cur = os.environ.get("DYLD_FRAMEWORK_PATH", "")
        os.environ["DYLD_FRAMEWORK_PATH"] = str(venv_qt) + (":" + cur if cur else "")
        cur_lib = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = str(venv_qt) + (":" + cur_lib if cur_lib else "")

    app = QApplication(sys.argv)
    app.setApplicationName("Crew")
    app.setWindowIcon(app_icon())
    # Normal macOS behaviour: closing the last window quits the app.
    # The qasync loop is stopped via app.aboutToQuit below.
    app.setQuitOnLastWindowClosed(True)

    # directories are per session now — no startup picker. This only sets the
    # engine's config root (global + project .crew); every session picks its
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
    app.aboutToQuit.connect(loop.stop)

    engine = Engine(project_dir)
    apply_theme(app, (engine.config.model_extra or {}).get("theme", "follow system"))
    from .app import sprites

    sprites.set_waifu(bool((engine.config.model_extra or {}).get("waifu")))
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
