"""Saved hosts — Termius-style connection book, Qt-free.

Hosts live in a single JSON file under the global dir (not per-project).
No passwords are ever stored: connections authenticate with SSH keys or the
agent, matching the rule that secrets never live in config files. See
docs/superpowers/specs/2026-08-05-hosts-and-rail-labels-design.md.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Host:
    id: str
    name: str
    hostname: str
    username: str = ""
    port: int = 22
    identity_file: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        target = f"{self.username}@{self.hostname}" if self.username else self.hostname
        return f"{self.name} ({target})" if self.name != target else target


def ssh_argv(host: Host) -> list[str]:
    """The ssh command for a host; omits defaults to keep it readable."""
    argv = ["ssh"]
    if host.port and host.port != 22:
        argv += ["-p", str(host.port)]
    if host.identity_file:
        argv += ["-i", str(Path(host.identity_file).expanduser())]
    target = f"{host.username}@{host.hostname}" if host.username else host.hostname
    argv.append(target)
    return argv


class HostStore:
    """JSON-backed host list with atomic writes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._hosts: list[Host] = self._load()

    def _load(self) -> list[Host]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        hosts = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                hosts.append(Host(
                    id=str(entry["id"]),
                    name=str(entry.get("name", "")),
                    hostname=str(entry["hostname"]),
                    username=str(entry.get("username", "")),
                    port=int(entry.get("port", 22)),
                    identity_file=str(entry.get("identity_file", "")),
                    created_at=float(entry.get("created_at", 0.0)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return hosts

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(h) for h in self._hosts], indent=2))
        os.replace(tmp, self.path)

    # -- CRUD -----------------------------------------------------------------

    def list(self) -> list[Host]:
        return list(self._hosts)

    def get(self, host_id: str) -> Host | None:
        return next((h for h in self._hosts if h.id == host_id), None)

    def add(
        self, *, name: str, hostname: str, username: str = "",
        port: int = 22, identity_file: str = "",
    ) -> Host:
        host = Host(
            id=uuid.uuid4().hex[:10],
            name=name.strip() or hostname,
            hostname=hostname.strip(),
            username=username.strip(),
            port=port,
            identity_file=identity_file.strip(),
        )
        self._hosts.append(host)
        self._save()
        return host

    def update(self, host: Host) -> None:
        for i, existing in enumerate(self._hosts):
            if existing.id == host.id:
                self._hosts[i] = host
                self._save()
                return

    def remove(self, host_id: str) -> None:
        before = len(self._hosts)
        self._hosts = [h for h in self._hosts if h.id != host_id]
        if len(self._hosts) != before:
            self._save()
