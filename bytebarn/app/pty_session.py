"""Interactive user PTY sessions (local shells) for the Terminal Manager.

Qt-free enough to unit-test the openpty helpers; the app layer owns lifecycle.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import signal
import struct
import termios
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


def _default_shell() -> str:
    return os.environ.get("SHELL") or "/bin/zsh"


@dataclass
class PtySession:
    """One interactive local shell attached to a PTY master fd."""

    id: str
    title: str
    cwd: str
    pid: int
    master_fd: int
    proc: asyncio.subprocess.Process
    status: str = "running"
    exit_code: int | None = None
    _reader_task: asyncio.Task | None = field(default=None, repr=False)
    _on_data: Callable[[str], None] | None = field(default=None, repr=False)
    _on_exit: Callable[[int | None], None] | None = field(default=None, repr=False)

    async def start_reader(
        self,
        on_data: Callable[[str], None],
        on_exit: Callable[[int | None], None] | None = None,
    ) -> None:
        self._on_data = on_data
        self._on_exit = on_exit
        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    data = await loop.run_in_executor(
                        None, lambda: os.read(self.master_fd, 4096))
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if self._on_data:
                    self._on_data(text)
        except asyncio.CancelledError:
            pass
        finally:
            code = self.proc.returncode
            if code is None:
                try:
                    code = await asyncio.wait_for(self.proc.wait(), 0.5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    code = None
            self.status = "exited"
            self.exit_code = code
            if self._on_exit:
                try:
                    self._on_exit(code)
                except Exception:
                    pass

    def write(self, data: str | bytes) -> None:
        if self.status != "running":
            return
        raw = data.encode("utf-8") if isinstance(data, str) else data
        try:
            os.write(self.master_fd, raw)
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        if self.status != "running" or rows < 1 or cols < 1:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            os.kill(self.pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self.proc.returncode is None:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(self.proc.wait(), 1.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(os.getpgid(self.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        self.status = "exited"


async def spawn_shell(
    *,
    terminal_id: str,
    cwd: str | Path | None = None,
    title: str = "",
    shell: str | None = None,
) -> PtySession:
    """Open a PTY and spawn the user shell."""
    shell = shell or _default_shell()
    work = str(Path(cwd or Path.home()).expanduser())
    master, slave = os.openpty()
    # Leave master blocking; reader uses executor.
    try:
        proc = await asyncio.create_subprocess_exec(
            shell,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=work,
            start_new_session=True,
            env={**os.environ, "TERM": "xterm-256color"},
        )
    finally:
        try:
            os.close(slave)
        except OSError:
            pass
    return PtySession(
        id=terminal_id,
        title=title or f"Shell · {Path(work).name}",
        cwd=work,
        pid=proc.pid or 0,
        master_fd=master,
        proc=proc,
    )
