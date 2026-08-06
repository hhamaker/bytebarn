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


KEY_AUTH = "key"
PASSWORD_AUTH = "password"


@dataclass
class Host:
    id: str
    name: str
    hostname: str
    username: str = ""
    port: int = 22
    identity_file: str = ""
    auth_type: str = KEY_AUTH   # "key" | "password"; the password itself
    # lives in AuthStore under "host:<id>", never in this file
    # Trust-on-first-use: add an unknown host key automatically instead of
    # asking. A *changed* key is still refused either way (MITM protection).
    accept_new_key: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        target = f"{self.username}@{self.hostname}" if self.username else self.hostname
        return f"{self.name} ({target})" if self.name != target else target


def ssh_argv(host: Host, command: str = "", *, batch: bool = False) -> list[str]:
    """The ssh command for a host, optionally running ``command`` remotely.

    ``batch`` is for unattended (agent) use: key auth then fails fast rather
    than hanging on a prompt. Interactive terminal connections leave it off so
    an encrypted key can still ask for its passphrase. Password auth asks
    exactly once and the password never touches argv — callers answer the
    prompt on a PTY."""
    argv = ["ssh"]
    if host.port and host.port != 22:
        argv += ["-p", str(host.port)]
    if host.accept_new_key:
        # accept-new adds unknown keys but still refuses changed ones
        argv += ["-o", "StrictHostKeyChecking=accept-new"]
    if host.auth_type == PASSWORD_AUTH:
        # Force the password path on regardless of what ~/.ssh/config says,
        # and keep ssh from spending the server's auth attempts offering keys
        # before it ever reaches the prompt.
        argv += ["-o", "PreferredAuthentications=password,keyboard-interactive",
                 "-o", "PasswordAuthentication=yes",
                 "-o", "KbdInteractiveAuthentication=yes",
                 "-o", "PubkeyAuthentication=no",
                 "-o", "IdentitiesOnly=yes",
                 "-o", "NumberOfPasswordPrompts=3"]
    else:
        if host.identity_file:
            argv += ["-i", str(Path(host.identity_file).expanduser())]
        if batch:
            argv += ["-o", "BatchMode=yes"]
    target = f"{host.username}@{host.hostname}" if host.username else host.hostname
    argv.append(target)
    if command:
        argv.append(command)
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
                    auth_type=str(entry.get("auth_type", KEY_AUTH)),
                    accept_new_key=bool(entry.get("accept_new_key", False)),
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

    def by_name(self, name: str) -> Host | None:
        """Lookup by id, exact name, or case-insensitive name (agent-friendly)."""
        needle = name.strip()
        for host in self._hosts:
            if host.id == needle or host.name == needle:
                return host
        lowered = needle.lower()
        return next((h for h in self._hosts if h.name.lower() == lowered), None)

    def add(
        self, *, name: str, hostname: str, username: str = "",
        port: int = 22, identity_file: str = "", auth_type: str = KEY_AUTH,
        accept_new_key: bool = False,
    ) -> Host:
        host = Host(
            id=uuid.uuid4().hex[:10],
            name=name.strip() or hostname,
            hostname=hostname.strip(),
            username=username.strip(),
            port=port,
            identity_file=identity_file.strip(),
            auth_type=auth_type if auth_type in (KEY_AUTH, PASSWORD_AUTH) else KEY_AUTH,
            accept_new_key=bool(accept_new_key),
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
