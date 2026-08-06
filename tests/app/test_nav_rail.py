"""Nav rail + view switching (spec: 2026-08-05-ui-redesign-design.md)."""

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


def _engine(tmp_path):
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    gdir = tmp_path / "g"
    gdir.mkdir(exist_ok=True)
    (gdir / "config.json").write_text(json.dumps({"model": "fake/m"}))
    return Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)


def test_rail_widget_shape(qapp):
    from bytebarn.app.nav_rail import VIEWS, NavRail

    rail = NavRail()
    assert rail.width() == 56 or rail.minimumWidth() == 56
    for view in VIEWS:
        assert view in rail._view_buttons
    assert rail.active_view() == "chat"
    rail.set_active("terminal")
    assert rail.active_view() == "terminal"


def test_rail_emits_view_and_tool_signals(qapp):
    from bytebarn.app.nav_rail import NavRail

    rail = NavRail()
    seen: list[str] = []
    rail.view_selected.connect(seen.append)
    rail.tool_selected.connect(seen.append)
    rail._view_buttons["code"].click()
    assert seen == ["code"]


def test_window_has_rail_and_clean_status_bar(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from PySide6.QtWidgets import QPushButton

    window = MainWindow(_engine(tmp_path))
    assert window.nav_rail is not None
    # providers/agents/settings moved from the status bar to the rail
    labels = [b.text() for b in window.statusBar().findChildren(QPushButton)]
    for gone in ("⚡ providers", "🐾 agents", "⚙ settings"):
        assert gone not in labels


def test_view_menu_shortcuts(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from PySide6.QtGui import QAction

    window = MainWindow(_engine(tmp_path))
    # assert on the window's QActions directly — re-wrapping QMenu objects
    # from menuBar().actions() is unstable under shiboken in this harness
    shortcuts = {a.text(): a.shortcut().toString()
                 for a in window.findChildren(QAction)}
    for label, key in (("Projects", "Ctrl+1"), ("Chat", "Ctrl+2"),
                       ("Code", "Ctrl+3"), ("Terminal", "Ctrl+4")):
        assert shortcuts.get(label) == key, (label, shortcuts.get(label))


async def test_set_view_switches_sidebar_and_tabs(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow

    engine = _engine(tmp_path)
    await engine.start()
    try:
        window = MainWindow(engine)
        window._set_view("projects")
        assert window.sidebar.currentIndex() == 0
        assert window.nav_rail.active_view() == "projects"

        window._set_view("code")
        await asyncio.sleep(0)
        assert window.sidebar.currentIndex() == 1
        assert window.workspace.tabs.currentIndex() == 1  # Goals fronts Code
        assert window.nav_rail.active_view() == "code"

        window._set_view("chat")
        await asyncio.sleep(0)
        assert window.workspace.tabs.currentIndex() == 0  # Chats fronts Chat
        assert window.nav_rail.active_view() == "chat"
    finally:
        await engine.stop()


async def test_terminal_view_swaps_content_and_restores(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow

    engine = _engine(tmp_path)
    await engine.start()
    try:
        window = MainWindow(engine)
        window.resize(1200, 800)
        window.show()
        qapp.processEvents()

        window._set_view("terminal")
        qapp.processEvents()
        assert window.terminal_panel.isVisible()
        assert not window.content_split.isVisible()
        assert not window.prompt_bar.isVisible()
        assert not window.header.isVisible()

        window._set_view("chat")
        qapp.processEvents()
        assert window.content_split.isVisible()
        assert window.prompt_bar.isVisible()
        assert window.header.isVisible()
        # bottom pane was closed before entering, so it stays closed
        assert not window.terminal_panel.isVisible()
    finally:
        await engine.stop()


async def test_terminal_view_keeps_open_bottom_pane_on_return(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow

    engine = _engine(tmp_path)
    await engine.start()
    try:
        window = MainWindow(engine)
        window.resize(1200, 800)
        window.show()
        qapp.processEvents()

        window._show_terminal()          # bottom pane open in chat view
        window._set_view("terminal")     # full view
        window._set_view("chat")         # back
        qapp.processEvents()
        assert window.terminal_panel.isVisible()  # pane restored, still open
    finally:
        await engine.stop()


def test_rail_expands_with_labels(qapp):
    from bytebarn.app.nav_rail import COLLAPSED_WIDTH, EXPANDED_WIDTH, NavRail

    rail = NavRail()
    assert rail.minimumWidth() == COLLAPSED_WIDTH
    assert rail._view_buttons["projects"].text() == "🛖"

    toggled: list[bool] = []
    rail.expanded_toggled.connect(toggled.append)
    rail.set_expanded(True, animate=False)
    assert rail.is_expanded()
    assert rail.minimumWidth() == EXPANDED_WIDTH
    assert rail._view_buttons["projects"].text() == "🛖  Projects"
    assert rail._view_buttons["terminal"].text() == ">_  Terminal"
    assert "Collapse" in rail.toggle_button.text()
    assert toggled == []  # programmatic set emits nothing

    rail.set_expanded(False, animate=False)
    assert rail.minimumWidth() == COLLAPSED_WIDTH
    assert rail._view_buttons["projects"].text() == "🛖"

    rail.toggle_button.click()  # user toggle emits
    assert toggled == [True]


async def test_rail_expansion_restored_from_config(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/m", "ui": {"rail_expanded": True}}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        window = MainWindow(engine)
        assert window.nav_rail.is_expanded()
    finally:
        await engine.stop()


async def test_new_code_session_uses_orchestrator(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow

    engine = _engine(tmp_path)
    await engine.start()
    try:
        window = MainWindow(engine)
        await window._new_session(agent="orchestrator")
        sessions = await engine.store.list_sessions(engine.project.id)
        assert sessions and sessions[0].agent == "orchestrator"
    finally:
        await engine.stop()
