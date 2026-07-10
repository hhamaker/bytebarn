"""OAuth / credential store for providers (~/.crew/auth.json).

Separate from config.json so secrets never leak into the (often committed)
project config. Stored as a flat map: provider name -> credential record.

Credential records:
    {"type": "oauth", "access": str, "refresh": str, "expires": int(ms)}
    {"type": "api",   "key": str}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import GLOBAL_DIR


class AuthStore:
    """Reads/writes provider credentials to a JSON file (0600 perms)."""

    def __init__(self, global_dir: Path | None = None):
        self.path = (global_dir or GLOBAL_DIR) / "auth.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - best effort on odd filesystems
            pass

    def all(self) -> dict[str, Any]:
        return self._load()

    def get(self, provider: str) -> dict[str, Any] | None:
        return self._load().get(provider)

    def set(self, provider: str, record: dict[str, Any]) -> None:
        data = self._load()
        data[provider] = record
        self._save(data)

    def remove(self, provider: str) -> None:
        data = self._load()
        if provider in data:
            del data[provider]
            self._save(data)


def is_expired(record: dict[str, Any], skew_ms: int = 0) -> bool:
    """True if an oauth record's access token is at/near expiry."""
    expires = record.get("expires")
    if not isinstance(expires, (int, float)):
        return False
    return expires - skew_ms <= time.time() * 1000
