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


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


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
    rows: int = 24
    cols: int = 80
    _reader_task: asyncio.Task | None = field(default=None, repr=False)
    _on_data: Callable[[str], None] | None = field(default=None, repr=False)
    _on_exit: Callable[[int | None], None] | None = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)

    async def start_reader(
        self,
        on_data: Callable[[str], None],
        on_exit: Callable[[int | None], None] | None = None,
    ) -> None:
        self._on_data = on_data
        self._on_exit = on_exit
        # Non-blocking master + asyncio reader avoids executor thrash and
        # delivers bytes as soon as the kernel has them.
        try:
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except OSError:
            pass
        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        fd = self.master_fd
        try:
            while not self._closed:
                try:
                    data = os.read(fd, 8192)
                except BlockingIOError:
                    fut: asyncio.Future = loop.create_future()

                    def _ready(f=fut) -> None:
                        if not f.done():
                            f.set_result(None)

                    try:
                        loop.add_reader(fd, _ready)
                    except (OSError, ValueError):
                        # fd gone
                        break
                    try:
                        await fut
                    finally:
                        try:
                            loop.remove_reader(fd)
                        except (OSError, ValueError):
                            pass
                    continue
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF, errno.EAGAIN):
                        if exc.errno == errno.EAGAIN:
                            await asyncio.sleep(0.01)
                            continue
                        break
                    raise
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if self._on_data:
                    try:
                        self._on_data(text)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            try:
                loop.remove_reader(fd)
            except (OSError, ValueError):
                pass
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
        if self.status != "running" or self._closed:
            return
        raw = data.encode("utf-8") if isinstance(data, str) else data
        try:
            # write may short; loop for large pastes
            view = memoryview(raw)
            while view:
                n = os.write(self.master_fd, view)
                if n <= 0:
                    break
                view = view[n:]
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        if self.status != "running" or self._closed:
            return
        rows = max(1, int(rows))
        cols = max(1, int(cols))
        if rows == self.rows and cols == self.cols:
            return
        self.rows = rows
        self.cols = cols
        try:
            _set_winsize(self.master_fd, rows, cols)
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except (ProcessLookupError, OSError):
                pass
        except (OSError, ProcessLookupError):
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        try:
            loop.remove_reader(self.master_fd)
        except (OSError, ValueError):
            pass
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
    rows: int = 24,
    cols: int = 80,
    command: list[str] | None = None,
) -> PtySession:
    """Open a PTY and spawn the user shell (or ``command``) with a winsize."""
    shell = shell or _default_shell()
    work = str(Path(cwd or Path.home()).expanduser())
    rows = max(1, rows)
    cols = max(1, cols)
    master, slave = os.openpty()
    try:
        _set_winsize(slave, rows, cols)
        # Also set on master so readers agree.
        try:
            _set_winsize(master, rows, cols)
        except OSError:
            pass
        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "LINES": str(rows),
            "COLUMNS": str(cols),
        }
        # Avoid nested-shell oddities if ByteBarn itself was launched from a
        # weird TERM (e.g. dumb under some launchers).
        env.pop("TERMINFO_DIRS", None)
        # Login + interactive so PATH matches Terminal.app (.zprofile / .bash_profile).
        if command:
            argv = list(command)
        else:
            base = os.path.basename(shell)
            if base in ("bash", "zsh", "sh", "fish"):
                argv = [shell, "-il"]
            else:
                argv = [shell]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=work,
            start_new_session=True,
            env=env,
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
        rows=rows,
        cols=cols,
    )
