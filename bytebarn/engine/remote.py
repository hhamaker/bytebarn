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
# openssh prompts: "user@host's password:", "Password:", "Enter passphrase…".
# Not anchored to the end of the buffer: when a login fails fast, one read can
# carry the prompt *and* the refusal, and an end-anchored pattern would miss
# it entirely. "Permission denied (publickey,password)." cannot match — the
# word is not followed by a colon on the same line.
PASSWORD_PROMPT = re.compile(r"(password|passphrase)[^\n]*:[ \t]*", re.IGNORECASE)


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

    tail = ""

    async def _pump() -> None:
        nonlocal total, answered, tail
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
            if needs_password and not answered:
                # match on a rolling tail: a PTY read can split the prompt
                # anywhere ("…passwo" + "rd: "), and either half alone matches
                # nothing
                tail = (tail + text)[-256:]
                if PASSWORD_PROMPT.search(tail.strip()):
                    answered = True
                    tail = ""
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
        # ssh turns echo off, but a stand-in (or a server-side prompt that
        # doesn't) would put the secret straight into the agent transcript
        if password:
            output = output.replace(password, "•••")
    if code == 124:
        output += f"\n[timed out after {timeout:g}s]"
    hint = host_key_hint(output, host, answered if needs_password else None)
    if hint:
        output += "\n" + hint
    return code, output


def host_key_hint(output: str, host: Host, answered: bool | None = None) -> str:
    """Turn ssh's terse host-key refusals into something actionable.

    Unattended runs cannot answer ssh's "are you sure?" prompt, so an unknown
    key looks like a bare "Host key verification failed." A *changed* key is a
    different matter — it can mean an interception, so we never suggest
    clearing it automatically."""
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in output:
        return (
            f"[{host.name}: the server's host key changed since it was first "
            "trusted. This can mean the server was rebuilt — or that the "
            "connection is being intercepted. Verify the new fingerprint out "
            "of band, then remove the old entry from ~/.ssh/known_hosts "
            "yourself. ByteBarn will not do it for you.]")
    if "Permission denied" in output:
        if host.auth_type == PASSWORD_AUTH:
            # ssh overwrites its own prompt with \r before printing the
            # refusal, so the transcript alone cannot say whether the password
            # was ever typed. `answered` is what actually happened.
            if answered is False:
                target = (f"{host.username}@{host.hostname}" if host.username
                          else host.hostname)
                return (
                    f"[{host.name}: no password prompt arrived, so the saved "
                    "one was never sent. Run `ssh -v " + target + "` in a "
                    "shell pane to see which methods the server actually "
                    "offers — the usual causes are PasswordAuthentication or "
                    "KbdInteractiveAuthentication being off in its "
                    f"sshd_config, or the username ({host.username or 'yours'})"
                    " being wrong.]")
            if answered:
                return (
                    f"[{host.name}: ByteBarn typed the saved password at the "
                    "server's prompt and the server rejected it. The stored "
                    "password is wrong or the account is locked — re-enter it "
                    "with Edit… on the host.]")
            return (
                f"[{host.name}: the server rejected the saved password. Check "
                "it in the host editor, and confirm the server allows password "
                "logins (PasswordAuthentication yes in its sshd_config).]")
        return (
            f"[{host.name}: the server rejected key authentication. Either add "
            "your public key to the server, or switch this host to Password "
            "auth in the host editor.]")
    if "Host key verification failed" in output:
        return (
            f"[{host.name}: its host key is not in ~/.ssh/known_hosts yet, and "
            "an unattended run cannot answer ssh's confirmation prompt. "
            "Connect once from the Terminal view and accept the fingerprint, "
            "or tick \"Trust new host key\" in the host editor to accept it "
            "automatically on first connect.]")
    return ""
