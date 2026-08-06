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

        async def fake_spawn(self, command=None, title="", password=None):
            spawned["command"] = command
            spawned["title"] = title
            spawned["password"] = password

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
                      "identity_file": "~/.ssh/db", "auth_type": "key",
                      "accept_new_key": False}
    assert dialog.password() == ""  # key auth never reports a password


def test_dialog_exposes_host_key_trust_opt_in(qapp):
    from bytebarn.app.host_dialog import HostDialog
    from bytebarn.engine.hosts import Host

    dialog = HostDialog()
    assert dialog.accept_new_check.isChecked() is False  # opt-in, never default
    dialog.accept_new_check.setChecked(True)
    assert dialog.values()["accept_new_key"] is True

    trusted = Host(id="h3", name="box", hostname="h", accept_new_key=True)
    assert HostDialog(trusted).accept_new_check.isChecked() is True


def test_host_dialog_password_mode(qapp):
    from PySide6.QtWidgets import QLineEdit

    from bytebarn.app.host_dialog import HostDialog
    from bytebarn.engine.hosts import PASSWORD_AUTH, Host

    host = Host(id="h2", name="legacy", hostname="10.0.0.9",
                username="root", auth_type=PASSWORD_AUTH)
    dialog = HostDialog(host, has_password=True)
    assert dialog.auth_type() == PASSWORD_AUTH
    assert dialog.password_edit.echoMode() == QLineEdit.Password
    assert "leave blank to keep" in dialog.password_edit.placeholderText()
    dialog.password_edit.setText("hunter2")
    assert dialog.password() == "hunter2"
    # the password is never part of the stored host record
    assert "hunter2" not in str(dialog.values())
    assert dialog.values()["auth_type"] == PASSWORD_AUTH
    assert dialog.values()["identity_file"] == ""


async def test_saving_password_host_keeps_secret_out_of_hosts_json(qapp, tmp_path):
    from bytebarn.app import terminal_panel as tp
    from bytebarn.app.host_dialog import HostDialog
    from bytebarn.engine.hosts import PASSWORD_AUTH

    engine = await _engine(tmp_path)
    try:
        panel = tp.TerminalPanel(engine)

        def fake_dialog(*args, **kwargs):
            dialog = HostDialog(*args, **kwargs)
            dialog.name_edit.setText("legacy")
            dialog.host_edit.setText("10.0.0.9")
            dialog.auth_combo.setCurrentIndex(1)  # Password
            dialog.password_edit.setText("hunter2")
            dialog.exec = lambda: 1  # accept without showing
            return dialog

        import bytebarn.app.host_dialog as hd

        original = hd.HostDialog
        hd.HostDialog = fake_dialog
        try:
            panel._add_host()
        finally:
            hd.HostDialog = original

        host = engine.hosts.by_name("legacy")
        assert host is not None and host.auth_type == PASSWORD_AUTH
        assert engine.host_password(host.id) == "hunter2"
        assert "hunter2" not in (engine.global_dir / "hosts.json").read_text()
    finally:
        await engine.stop()
