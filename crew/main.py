"""Crew entry point: qasync event loop + engine + main window."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import qasync
from PySide6.QtWidgets import QApplication, QFileDialog

from .app.main_window import MainWindow
from .app.theme import apply_theme
from .engine.facade import Engine


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Crew")

    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).resolve()
    else:
        picked = QFileDialog.getExistingDirectory(None, "Open project directory")
        if not picked:
            sys.exit(0)
        project_dir = Path(picked)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    engine = Engine(project_dir)
    apply_theme(app, (engine.config.model_extra or {}).get("theme", "follow system"))
    window = MainWindow(engine)

    async def bootstrap() -> None:
        await engine.start()
        await window.bootstrap()
        window.show()

    with loop:
        loop.create_task(bootstrap())
        loop.run_forever()


if __name__ == "__main__":
    main()
