"""HostStore + ssh_argv (spec: 2026-08-05-hosts-and-rail-labels-design.md)."""

import dataclasses
import json

from bytebarn.engine.hosts import Host, HostStore, ssh_argv


def test_crud_roundtrip(tmp_path):
    path = tmp_path / "hosts.json"
    store = HostStore(path)
    assert store.list() == []

    host = store.add(name="web", hostname="example.com",
                     username="deploy", port=2222,
                     identity_file="~/.ssh/id_ed25519")
    assert store.get(host.id).hostname == "example.com"
    # file parses after every write and a fresh store sees the same data
    assert json.loads(path.read_text())[0]["name"] == "web"
    again = HostStore(path)
    assert [h.id for h in again.list()] == [host.id]

    updated = dataclasses.replace(host, name="web-2", port=22)
    store.update(updated)
    assert HostStore(path).get(host.id).name == "web-2"

    store.remove(host.id)
    assert HostStore(path).list() == []


def test_ssh_argv_variants():
    base = Host(id="x", name="n", hostname="example.com")
    assert ssh_argv(base) == ["ssh", "example.com"]
    full = Host(id="x", name="n", hostname="example.com",
                username="deploy", port=2222, identity_file="~/.ssh/key")
    argv = ssh_argv(full)
    assert argv[0] == "ssh"
    assert "-p" in argv and "2222" in argv
    assert "-i" in argv
    assert argv[-1] == "deploy@example.com"
    assert "~" not in argv[argv.index("-i") + 1]  # identity expanded


def test_no_password_field_exists():
    fields = {f.name for f in dataclasses.fields(Host)}
    assert "password" not in fields and "secret" not in fields


def test_malformed_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "hosts.json"
    path.write_text("{not json")
    store = HostStore(path)
    assert store.list() == []
    store.add(name="a", hostname="b")  # next save rewrites the file
    assert len(json.loads(path.read_text())) == 1


def test_defaults_and_blank_name(tmp_path):
    store = HostStore(tmp_path / "hosts.json")
    host = store.add(name="  ", hostname="10.0.0.5")
    assert host.name == "10.0.0.5"
    assert host.port == 22
    assert "10.0.0.5" in host.label
