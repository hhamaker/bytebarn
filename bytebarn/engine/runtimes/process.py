"""Shared subprocess helpers for external runtimes."""

from __future__ import annotations

import asyncio
import os


async def kill_process_group(proc: asyncio.subprocess.Process | None) -> None:
    """SIGKILL the process group (or the process) if it is still running.

    Mirrors ``sandbox._kill_process_group`` so runtimes don't import private
    sandbox symbols. The kill itself is synchronous; waiting for exit is
    best-effort. ``CancelledError`` from the wait is swallowed so callers can
    finish cleanup.
    """
    if proc is None or proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    try:
        await asyncio.wait_for(proc.wait(), 1.0)
    except (asyncio.TimeoutError, ProcessLookupError, OSError, asyncio.CancelledError):
        pass
