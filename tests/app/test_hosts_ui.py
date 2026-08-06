"""Hosts in the Terminal Manager (spec: 2026-08-05-hosts-and-rail-labels)."""

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


async def test_panel_lists_hosts_and_connects(qapp, tmp_path, monkeypatch):
    from bytebarn.app import terminal_panel as tp

    engine = await _engine(tmp_path)
    try:
        host = engine.hosts.add(name="web", hostname="example.com",
                                username="deploy")
        panel = tp.TerminalPanel(engine)
        labels = [panel.host_list.item(i).text()
                  for i in range(panel.host_list.count())]
        assert any("web" in text and "deploy@example.com" in text
                   for text in labels)

        spawned: dict = {}

        async def fake_spawn(self, command=None, title=""):
            spawned["command"] = command
            spawned["title"] = title

        monkeypatch.setattr(tp.TerminalPanel, "_spawn_shell", fake_spawn)
        panel._connect_host(host.id)
        await asyncio.sleep(0)
        assert spawned["command"] == ["ssh", "deploy@example.com"]
        assert spawned["title"] == "web"

        engine.hosts.remove(host.id)
        panel._reload_hosts()
        assert panel.host_list.count() == 0
    finally:
        await engine.stop()


def test_host_dialog_round_trip(qapp):
    from bytebarn.app.host_dialog import HostDialog
    from bytebarn.engine.hosts import Host

    host = Host(id="h1", name="db box", hostname="db.internal",
                username="admin", port=2200, identity_file="~/.ssh/db")
    dialog = HostDialog(host)
    values = dialog.values()
    assert values == {"name": "db box", "hostname": "db.internal",
                      "username": "admin", "port": 2200,
                      "identity_file": "~/.ssh/db"}
    # no password widget anywhere in the form
    from PySide6.QtWidgets import QLineEdit

    assert all(e.echoMode() == QLineEdit.Normal
               for e in dialog.findChildren(QLineEdit))
