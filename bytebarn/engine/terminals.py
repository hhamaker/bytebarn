"""Process / terminal hub — Qt-free registry of backend process output.

Claude Code (and later other backends) tee stdout/stderr here so the UI can
show a "underlying terminal" view without stealing the single-consumer pipe.
Interactive user PTYs live in the app layer and only appear in the UI list.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventBus

# Cap memory per terminal (~512 KiB of text)
_MAX_CHARS = 512_000
# Coalesce UI events
_FLUSH_CHARS = 4_096
_FLUSH_SEC = 0.05


@dataclass
class TerminalInfo:
    id: str
    kind: str  # claude-code | user | bash
    title: str = ""
    session_id: str = ""
    cwd: str = ""
    pid: int | None = None
    status: str = "running"  # running | exited
    interactive: bool = False
    exit_code: int | None = None
    opened_at: float = field(default_factory=time.time)


@dataclass
class _Buffer:
    info: TerminalInfo
    chunks: deque[str] = field(default_factory=deque)
    chars: int = 0
    pending: list[str] = field(default_factory=list)
    pending_chars: int = 0
    last_emit: float = 0.0


class ProcessHub:
    """In-memory terminal registry + rolling output buffers."""

    def __init__(self, bus: EventBus | None = None):
        self._bus = bus
        self._terms: dict[str, _Buffer] = {}

    # -- lifecycle -----------------------------------------------------------

    def open(
        self,
        *,
        kind: str,
        title: str = "",
        session_id: str = "",
        cwd: str = "",
        pid: int | None = None,
        interactive: bool = False,
        terminal_id: str | None = None,
    ) -> str:
        tid = terminal_id or f"{kind}:{uuid.uuid4().hex[:10]}"
        info = TerminalInfo(
            id=tid,
            kind=kind,
            title=title or tid,
            session_id=session_id,
            cwd=cwd,
            pid=pid,
            interactive=interactive,
        )
        self._terms[tid] = _Buffer(info=info, last_emit=time.monotonic())
        if self._bus is not None:
            from .events import TerminalOpened

            self._bus.emit(TerminalOpened(
                terminal_id=tid,
                kind=kind,
                session_id=session_id,
                title=info.title,
                cwd=cwd,
                pid=pid if pid is not None else -1,
                interactive=interactive,
            ))
        return tid

    def append(self, terminal_id: str, text: str, stream: str = "stdout") -> None:
        buf = self._terms.get(terminal_id)
        if buf is None or not text:
            return
        if stream == "stderr" and not text.startswith("[stderr]"):
            # Keep a light marker so the UI can style if wanted
            pass
        buf.chunks.append(text)
        buf.chars += len(text)
        while buf.chars > _MAX_CHARS and buf.chunks:
            dropped = buf.chunks.popleft()
            buf.chars -= len(dropped)
        buf.pending.append(text)
        buf.pending_chars += len(text)
        now = time.monotonic()
        if (buf.pending_chars >= _FLUSH_CHARS
                or (now - buf.last_emit) >= _FLUSH_SEC):
            self._flush(buf)

    def close(self, terminal_id: str, exit_code: int | None = None) -> None:
        buf = self._terms.get(terminal_id)
        if buf is None:
            return
        self._flush(buf)
        buf.info.status = "exited"
        buf.info.exit_code = exit_code
        if self._bus is not None:
            from .events import TerminalClosed

            self._bus.emit(TerminalClosed(
                terminal_id=terminal_id,
                exit_code=exit_code if exit_code is not None else -1,
            ))

    def clear(self) -> None:
        """Drop all terminals (engine stop)."""
        for tid in list(self._terms):
            buf = self._terms[tid]
            if buf.info.status == "running":
                self.close(tid, exit_code=None)
        self._terms.clear()

    # -- reads ---------------------------------------------------------------

    def list(self) -> list[TerminalInfo]:
        return [b.info for b in sorted(
            self._terms.values(), key=lambda b: b.info.opened_at, reverse=True)]

    def get(self, terminal_id: str) -> TerminalInfo | None:
        buf = self._terms.get(terminal_id)
        return buf.info if buf else None

    def snapshot(self, terminal_id: str, max_chars: int = 200_000) -> str:
        buf = self._terms.get(terminal_id)
        if buf is None:
            return ""
        # flush pending so snapshot is complete
        self._flush(buf)
        text = "".join(buf.chunks)
        if len(text) > max_chars:
            return text[-max_chars:]
        return text

    def remove(self, terminal_id: str) -> None:
        """Forget a terminal entry (UI closed a finished backend)."""
        self._terms.pop(terminal_id, None)

    # -- internals -----------------------------------------------------------

    def _flush(self, buf: _Buffer) -> None:
        if not buf.pending:
            return
        text = "".join(buf.pending)
        buf.pending.clear()
        buf.pending_chars = 0
        buf.last_emit = time.monotonic()
        if self._bus is not None and text:
            from .events import TerminalChunk

            self._bus.emit(TerminalChunk(
                terminal_id=buf.info.id,
                text=text,
                stream="stdout",
            ))
