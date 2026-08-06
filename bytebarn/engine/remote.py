"""Run one command on a saved host over ssh — asyncio, no Qt.

The command runs on a PTY so a password prompt can be answered from memory:
the password never appears in argv (where ``ps`` would show it) and is never
written to disk outside the 0600 auth store. See
docs/superpowers/specs/2026-08-05-host-passwords-and-agent-ssh-design.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pty
import re
import signal

from .hosts import PASSWORD_AUTH, Host, ssh_argv

DEFAULT_TIMEOUT = 120.0
_MAX_OUTPUT = 200_000
# openssh prompts: "user@host's password:", "Password:", "Enter passphrase…"
PASSWORD_PROMPT = re.compile(r"(password|passphrase)[^\n]*:\s*$", re.IGNORECASE)


async def run_remote(
    host: Host,
    command: str,
    *,
    password: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    argv: list[str] | None = None,
) -> tuple[int, str]:
    """Run ``command`` on ``host``; returns (exit code, combined output).

    Exit code 124 means the command timed out, matching ``timeout(1)``."""
    argv = argv or ssh_argv(host, command, batch=True)
    master, slave = pty.openpty()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True,
        )
    finally:
        os.close(slave)

    needs_password = host.auth_type == PASSWORD_AUTH and password is not None
    chunks: list[str] = []
    total = 0
    answered = False
    loop = asyncio.get_running_loop()

    async def _pump() -> None:
        nonlocal total, answered
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master, 8192)
            except OSError:
                return
            if not data:
                return
            text = data.decode("utf-8", errors="replace")
            total += len(text)
            if total <= _MAX_OUTPUT:
                chunks.append(text)
            if needs_password and not answered and PASSWORD_PROMPT.search(text.strip()):
                answered = True
                os.write(master, (password + "\n").encode())

    pump = asyncio.ensure_future(_pump())
    code = 0
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()
        code = 124
    finally:
        # let the pump drain whatever is still buffered, then close the fd
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(pump), 0.4)
        pump.cancel()
        with contextlib.suppress(Exception):
            await pump
        with contextlib.suppress(OSError):
            os.close(master)

    output = "".join(chunks)
    if total > _MAX_OUTPUT:
        output += f"\n[output truncated at {_MAX_OUTPUT} chars]"
    if needs_password:
        # strip the echoed prompt line so the transcript stays clean
        output = PASSWORD_PROMPT.sub("", output, count=1)
    if code == 124:
        output += f"\n[timed out after {timeout:g}s]"
    return code, output
