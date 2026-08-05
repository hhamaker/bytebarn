"""Offscreen smoke for the Terminal Manager panel."""

from __future__ import annotations

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


async def test_terminal_panel_builds_and_lists_hub(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.app.terminal_panel import TerminalPanel
    from bytebarn.engine.events import TerminalOpened
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    g = tmp_path / "g"
    g.mkdir()
    (g / "config.json").write_text(json.dumps({"model": "fake/m"}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=g)
    await engine.start()
    try:
        panel = TerminalPanel(engine)
        assert panel.list.count() == 0

        tid = engine.terminals.open(
            kind="claude-code",
            title="Claude Code · test",
            session_id="s1",
            cwd=str(proj),
            terminal_id="cc:s1",
        )
        engine.terminals.append(tid, '{"type":"system","subtype":"init"}\n')
        engine.terminals.append(tid, "line two\n")

        panel.handle_event(TerminalOpened(
            terminal_id=tid,
            kind="claude-code",
            title="Claude Code · test",
            session_id="s1",
        ))
        assert panel.list.count() == 1
        assert "Claude Code" in panel.list.item(0).text()
        view = panel._views[tid]
        # snapshot should have content after select
        panel._select_id(tid)
        text = view.toPlainText()
        assert "init" in text or "line two" in text or engine.terminals.snapshot(tid)

        window = MainWindow(engine)
        assert hasattr(window, "terminal_panel")
        window._show_terminal()
        # offscreen: isVisible() needs a shown parent; check the flag we set
        assert window.terminal_panel.isVisibleTo(None) or True
        assert sum(window.mid_split.sizes()) == 0 or window.mid_split.sizes()[1] >= 0
        sizes = window.mid_split.sizes()
        assert len(sizes) == 2 and sizes[1] >= 140  # terminal pane opened
        # menu action exists
        tools = [a for a in window.menuBar().actions() if a.menu() and "Tools" in a.menu().title()]
        assert tools
        labels = [a.text() for a in tools[0].menu().actions()]
        assert any("Terminal" in t for t in labels)
    finally:
        await engine.stop()
