"""Pane splits, renames, and per-terminal themes
(spec: 2026-08-05-terminal-splits-themes-design.md)."""

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


def _open_backend(engine, panel, tid="cc:s1", title="Claude Code · test"):
    from bytebarn.engine.events import TerminalOpened

    engine.terminals.open(kind="claude-code", title=title,
                          session_id="s1", terminal_id=tid)
    panel.handle_event(TerminalOpened(
        terminal_id=tid, kind="claude-code", title=title, session_id="s1"))
    return tid


async def _engine(tmp_path):
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    g = tmp_path / "g"
    g.mkdir()
    (g / "config.json").write_text(json.dumps({"model": "fake/m"}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=g)
    await engine.start()
    return engine


def test_theme_catalog_shape():
    from bytebarn.app.term_themes import DEFAULT_THEME, THEMES, get_theme

    assert DEFAULT_THEME in THEMES
    assert len(THEMES) >= 8
    for theme in THEMES.values():
        assert len(theme.ansi) == 16
        for spec in (theme.fg, theme.bg, theme.cursor, *theme.ansi):
            assert spec.startswith("#") and len(spec) == 7, (theme.name, spec)
            int(spec[1:], 16)
        assert theme.selection.startswith("#") and len(theme.selection) in (7, 9)
    assert get_theme("no-such-theme").name == DEFAULT_THEME


def test_split_and_close_panes(qapp):
    from PySide6.QtCore import Qt

    from bytebarn.app.pane_area import PaneArea

    area = PaneArea()
    assert len(area.panes()) == 1
    first = area.active_pane()
    second = area.split(first, Qt.Horizontal)
    assert len(area.panes()) == 2
    assert area.active_pane() is second
    third = area.split(second, Qt.Vertical)
    assert len(area.panes()) == 3
    area.close_pane(third)
    assert len(area.panes()) == 2
    area.close_pane(second)
    assert area.panes() == [first]
    # last pane never removed, only cleared
    area.close_pane(first)
    assert area.panes() == [first]


async def test_select_mounts_into_active_pane(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from bytebarn.app.terminal_panel import TerminalPanel

    engine = await _engine(tmp_path)
    try:
        panel = TerminalPanel(engine)
        tid1 = _open_backend(engine, panel, "cc:s1", "one")
        pane1 = panel.pane_area.active_pane()
        assert pane1.terminal_id == tid1
        assert pane1.view() is panel._views[tid1]

        pane2 = panel.pane_area.split(pane1, Qt.Horizontal)
        panel._wire_pane(pane2)
        tid2 = _open_backend(engine, panel, "cc:s2", "two")
        assert pane2.terminal_id == tid2
        # both terminals visible side by side
        assert pane1.terminal_id == tid1

        # selecting an already-tiled terminal focuses its pane, no re-mount
        panel._select_id(tid1)
        assert panel.pane_area.active_pane() is pane1

        # closing a pane parks the view; terminal stays listed
        panel._pane_closed(pane2)
        assert len(panel.pane_area.panes()) == 1
        assert tid2 in panel._views
        assert panel._find_item(tid2) is not None
    finally:
        await engine.stop()


async def test_rename_updates_list_pane_and_hub(qapp, tmp_path):
    from bytebarn.app.terminal_panel import TerminalPanel

    engine = await _engine(tmp_path)
    try:
        panel = TerminalPanel(engine)
        tid = _open_backend(engine, panel, "cc:s1", "old name")

        # core rename path without the modal dialog
        panel._custom_titles[tid] = "my build log"
        engine.terminals.rename(tid, "my build log")
        panel._find_item(tid).setText(panel._label(tid))
        pane = panel.pane_area.pane_for(tid)
        pane.set_title(panel._display_title(tid))

        assert "my build log" in panel._find_item(tid).text()
        assert engine.terminals.get(tid).title == "my build log"
        # refresh cannot resurrect the old name
        panel.refresh_from_hub()
        assert "my build log" in panel._find_item(tid).text()

        # hub ignores blank renames
        engine.terminals.rename(tid, "   ")
        assert engine.terminals.get(tid).title == "my build log"
    finally:
        await engine.stop()


async def test_theme_applies_and_default_from_config(qapp, tmp_path):
    from bytebarn.app.term_themes import get_theme
    from bytebarn.app.terminal_panel import TerminalPanel, TerminalView

    engine = await _engine(tmp_path)
    try:
        panel = TerminalPanel(engine)
        tid = _open_backend(engine, panel)
        panel._set_terminal_theme(tid, "Dracula")
        assert panel._theme_names[tid] == "Dracula"
        assert panel._views[tid].theme_name() == "Dracula"

        view = TerminalView(interactive=False)
        assert view.theme_name() == "Dark+"
        view.set_theme(get_theme("Nord"))
        assert view._bg.name() == "#2e3440"
        view.feed("\x1b[31mred\x1b[0m")
        assert view._resolve(1, is_fg=True).name() == "#bf616a"
    finally:
        await engine.stop()


def test_drop_zone_geometry():
    from PySide6.QtCore import QPoint

    from bytebarn.app.pane_area import zone_for, zone_rect

    w, h = 400, 200
    assert zone_for(QPoint(10, 100), w, h) == "left"
    assert zone_for(QPoint(390, 100), w, h) == "right"
    assert zone_for(QPoint(200, 10), w, h) == "top"
    assert zone_for(QPoint(200, 190), w, h) == "bottom"
    assert zone_for(QPoint(200, 100), w, h) == "center"
    assert zone_rect("left", w, h).width() == w // 2
    assert zone_rect("center", w, h).width() == w


async def test_drop_splits_and_moves(qapp, tmp_path):
    from bytebarn.app.terminal_panel import TerminalPanel

    engine = await _engine(tmp_path)
    try:
        panel = TerminalPanel(engine)
        tid1 = _open_backend(engine, panel, "cc:s1", "one")
        tid2 = _open_backend(engine, panel, "cc:s2", "two")
        pane1 = panel.pane_area.pane_for(tid2) or panel.pane_area.active_pane()

        # drop tid1 on the right edge → new pane to the right with tid1
        panel._terminal_dropped(pane1, tid1, "right")
        assert len(panel.pane_area.panes()) == 2
        new_pane = panel.pane_area.pane_for(tid1)
        assert new_pane is not None and new_pane is not pane1

        # drop tid2 onto tid1's pane center → moves tid2 there, old pane empties
        panel._terminal_dropped(new_pane, tid2, "center")
        assert new_pane.terminal_id == tid2
        assert pane1.terminal_id is None
        assert panel.pane_area.pane_for(tid1) is None  # tid1 parked
        assert tid1 in panel._views

        # drop tid1 on top edge of tid2's pane → splits vertically, before
        panel._terminal_dropped(new_pane, tid1, "top")
        assert len(panel.pane_area.panes()) == 3
        assert panel.pane_area.pane_for(tid1).terminal_id == tid1
    finally:
        await engine.stop()


async def test_default_theme_config_key(qapp, tmp_path):
    from bytebarn.engine.facade import Engine

    from bytebarn.app.terminal_panel import TerminalPanel

    proj = tmp_path / "p2"
    proj.mkdir()
    g = tmp_path / "g2"
    g.mkdir()
    (g / "config.json").write_text(json.dumps(
        {"model": "fake/m", "terminal": {"theme": "Gruvbox Dark"}}))
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=g)
    await engine.start()
    try:
        panel = TerminalPanel(engine)
        assert panel._default_theme_name() == "Gruvbox Dark"
        tid = _open_backend(engine, panel)
        assert panel._views[tid].theme_name() == "Gruvbox Dark"
    finally:
        await engine.stop()
